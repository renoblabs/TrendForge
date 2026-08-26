from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy.orm import Session

from trendforge.acquisition.apify import ApifyClient
from trendforge.acquisition.errors import AcquisitionError
from trendforge.acquisition.service import get_provider, run_acquisition
from trendforge.config import load_data_sources_config, load_discovery_config
from trendforge.discovery.pipeline import apply_signals, record_observation, refresh_candidate_metrics
from trendforge.discovery.profiles import keep_language_candidate
from trendforge.discovery.tiktok import (
    _apify_tiktok_cfg,
    _base_actor_input,
    item_to_video,
    tiktok_video_id,
)
from trendforge.models import (
    AcquisitionRun,
    CandidateObservation,
    CohortDifferentialAnalysis,
    ContentCandidate,
    DiscoveryRun,
    ResearchCohort,
    ResearchCohortMember,
    utcnow,
)
from trendforge.research.config import (
    build_config_snapshot,
    cohort_targets,
    composite_config_snapshot,
    config_drift,
    config_fingerprint,
    ensure_utc,
    format_toronto,
)

MATURE_COHORT_KEY = "legacy-tiktok-fresh-search-mature-v1"
COHORT_24_KEY = "acquisition-run-24"
TRAJECTORY_RULE_VERSION = "trajectory-v1"

# These provider identifiers/costs were verified against the corresponding
# Apify runs during the original measurements. They are historical provenance,
# not estimates. Backfill records their origin explicitly in run metadata.
HISTORICAL_OBSERVATION_PROVENANCE: dict[int, dict[str, Any]] = {
    13: {
        "apify_run_id": "ipaGJ3Qsd6ck6pNSt",
        "apify_dataset_id": "wcX2eool8YZh62P5o",
        "actual_cost": 0.0491,
        "currency": "USD",
    },
    15: {
        "apify_run_id": "KBmshST0qSCcNFa7S",
        "apify_dataset_id": "wEjZYzxgQIKi4SsdF",
        "actual_cost": 0.0491,
        "currency": "USD",
    },
    16: {
        "apify_run_id": "gCWTN4lpwDK8Ypd6d",
        "apify_dataset_id": "Cp4eyEecW3nirT1N1",
        "actual_cost": 0.075,
        "currency": "USD",
    },
}


@dataclass
class CohortObservationResult:
    cohort: ResearchCohort
    milestone: str
    run: DiscoveryRun | None
    requested_candidate_ids: list[int]
    observed_candidate_ids: list[int]
    censored_candidate_ids: list[int]
    unavailable_candidate_ids: list[int]
    no_op: bool = False


def classify_trajectory(t0_views: int | None, latest_views: int | None) -> str | None:
    """Apply the descriptive, non-predictive trajectory-v1 outcome rules."""
    if t0_views is None or latest_views is None:
        return None
    growth = latest_views / max(t0_views, 1)
    absolute_growth = latest_views - t0_views
    if latest_views >= 100_000 and (growth >= 10 or absolute_growth >= 1_000_000):
        return "BREAKOUT"
    if latest_views >= 10_000 and growth >= 5:
        return "STRONG_RISER"
    if latest_views >= 1_000:
        return "MODERATE"
    return "STALLED"


def _profile_run_snapshot(run: AcquisitionRun) -> dict[str, Any]:
    """Reconstruct a secret-free snapshot from configuration persisted on a run."""
    meta = dict(run.run_metadata_json or {})
    actor_input = dict(meta.get("actor_input") or {})
    profile = str(run.profile or meta.get("profile") or "")
    limit = int(meta.get("limit") or 25)
    # Start with the same allowlisted shape used for current-profile drift
    # checks, then replace behavior fields with what this historical run
    # actually persisted.
    snapshot = build_config_snapshot(run.source, profile, limit)
    safe_actor_keys = set((snapshot.get("actor_input") or {}).keys())
    snapshot.update(
        {
            "source": run.source,
            "provider": run.provider,
            "profile": profile,
            "actor_id": run.actor_id,
            "queries": list(meta.get("queries") or actor_input.get("searchQueries") or []),
            "ordering": actor_input.get("videoSearchSorting") or actor_input.get("sort"),
            "freshness_window": meta.get("window")
            or actor_input.get("videoSearchDateFilter")
            or actor_input.get("datePosted"),
            "result_limit": limit,
            "actor_input": {
                key: actor_input[key] for key in safe_actor_keys if key in actor_input
            },
        }
    )
    return snapshot


def _raw_candidate_ids_for_run(db: Session, run: AcquisitionRun) -> list[int]:
    """Recover candidates selected by an old run without trusting mutable origin fields."""
    meta = dict(run.run_metadata_json or {})
    stored = meta.get("kept_candidate_ids")
    if isinstance(stored, list):
        return list(dict.fromkeys(int(x) for x in stored))
    raw_path = meta.get("raw_dataset_path")
    if not raw_path or not Path(raw_path).is_file():
        return []
    try:
        items = json.loads(Path(raw_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    provider = get_provider(run.source)
    actor_input = meta.get("actor_input") or {}
    max_short = float((meta.get("max_short_seconds") or 60))
    ids: list[int] = []
    for rank, raw in enumerate(items if isinstance(items, list) else []):
        if not isinstance(raw, dict):
            continue
        normalized = provider.normalize(
            raw,
            search_rank=rank,
            max_short_seconds=max_short,
            profile=run.profile,
        )
        if (
            normalized is None
            or normalized.is_short is False
            or not keep_language_candidate(normalized.language_signal or "unknown")
        ):
            continue
        candidate = (
            db.query(ContentCandidate)
            .filter(
                ContentCandidate.platform == run.source,
                ContentCandidate.external_id == normalized.external_id,
            )
            .one_or_none()
        )
        if candidate is not None:
            ids.append(candidate.id)
    return list(dict.fromkeys(ids))


def _observations_during(
    db: Session,
    candidate_id: int,
    started_at: datetime,
    completed_at: datetime,
    *,
    source: str | None = None,
) -> list[CandidateObservation]:
    q = db.query(CandidateObservation).filter(
        CandidateObservation.candidate_id == candidate_id,
        CandidateObservation.observed_at >= started_at,
        CandidateObservation.observed_at <= completed_at + timedelta(seconds=1),
    )
    if source:
        q = q.filter(CandidateObservation.source == source)
    return q.order_by(CandidateObservation.observed_at.asc(), CandidateObservation.id.asc()).all()


def _latest_valid_observation(
    db: Session, candidate_id: int, *, before: datetime | None = None
) -> CandidateObservation | None:
    q = db.query(CandidateObservation).filter(
        CandidateObservation.candidate_id == candidate_id,
        CandidateObservation.view_count.isnot(None),
    )
    if before is not None:
        q = q.filter(CandidateObservation.observed_at <= before)
    return q.order_by(CandidateObservation.observed_at.desc(), CandidateObservation.id.desc()).first()


def _get_or_create_cohort(db: Session, cohort_key: str, **values: Any) -> tuple[ResearchCohort, bool]:
    cohort = db.query(ResearchCohort).filter(ResearchCohort.cohort_key == cohort_key).one_or_none()
    if cohort is not None:
        return cohort, False
    cohort = ResearchCohort(cohort_key=cohort_key, **values)
    db.add(cohort)
    db.flush()
    return cohort, True


def _get_or_create_member(
    db: Session, cohort: ResearchCohort, candidate_id: int
) -> ResearchCohortMember:
    member = (
        db.query(ResearchCohortMember)
        .filter(
            ResearchCohortMember.cohort_id == cohort.id,
            ResearchCohortMember.candidate_id == candidate_id,
        )
        .one_or_none()
    )
    if member is None:
        member = ResearchCohortMember(
            cohort_id=cohort.id,
            candidate_id=candidate_id,
            included_at=utcnow(),
            outcome_rule_version=TRAJECTORY_RULE_VERSION,
        )
        db.add(member)
        db.flush()
    return member


def _attach_historical_run(
    run: DiscoveryRun | None, *, cohort_id: int, milestone: str
) -> None:
    if run is None:
        return
    run.cohort_id = cohort_id
    run.milestone = milestone
    provenance = HISTORICAL_OBSERVATION_PROVENANCE.get(run.id)
    if provenance:
        for key in ("apify_run_id", "apify_dataset_id", "actual_cost", "currency"):
            if getattr(run, key, None) is None:
                setattr(run, key, provenance.get(key))
        metadata = dict(run.run_metadata_json or {})
        metadata.setdefault(
            "provider_provenance",
            "verified historical Apify run metadata; backfilled without an additional call",
        )
        run.run_metadata_json = metadata


def backfill_mature_reference(db: Session) -> ResearchCohort:
    source_runs = [db.get(AcquisitionRun, rid) for rid in (18, 22)]
    if any(run is None for run in source_runs):
        raise ValueError("Mature cohort backfill requires acquisition runs #18 and #22")
    runs = [run for run in source_runs if run is not None]
    t1_run = db.get(DiscoveryRun, 13)
    t2_run = db.get(DiscoveryRun, 15)
    if t1_run is None or t2_run is None:
        raise ValueError("Mature cohort backfill requires discovery runs #13 and #15")

    member_ids: list[int] = []
    for run in runs:
        member_ids.extend(_raw_candidate_ids_for_run(db, run))
    member_ids = list(dict.fromkeys(member_ids))
    if len(member_ids) != 13:
        raise ValueError(f"Expected 13 mature cohort members, found {len(member_ids)}")

    snapshots = [
        {"run_id": run.id, "snapshot": _profile_run_snapshot(run)} for run in runs
    ]
    snapshot = composite_config_snapshot(snapshots)
    t0_at = max(ensure_utc(run.completed_at or run.started_at) for run in runs)
    targets = cohort_targets(t0_at, "tiktok_fresh_search")
    cohort, created = _get_or_create_cohort(
        db,
        MATURE_COHORT_KEY,
        name="Mature TikTok Fresh Search Reference Cohort",
        source="tiktok",
        provider="apify",
        profile="tiktok_fresh_search",
        acquisition_run_id=None,
        source_run_ids_json=[18, 22],
        created_at=utcnow(),
        t0_at=t0_at,
        target_t1_window_start=targets["target_t1_window_start"],
        target_t1_at=targets["target_t1_at"],
        target_t1_window_end=targets["target_t1_window_end"],
        target_t2_at=targets["target_t2_at"],
        status="COMPLETED",
        config_fingerprint=config_fingerprint(snapshot),
        config_snapshot_json=snapshot,
        notes=(
            "Legacy composite selected by acquisition runs #18 and #22; candidate 222 "
            "originated earlier and was reselected by run #22. Member-specific T0 observations "
            "are preserved. Run #18 and #22 configurations differ."
        ),
        outcome_rule_version=TRAJECTORY_RULE_VERSION,
    )
    if not created:
        cohort.source_run_ids_json = [18, 22]
        cohort.status = "COMPLETED"

    for candidate_id in member_ids:
        member = _get_or_create_member(db, cohort, candidate_id)
        t0_options: list[CandidateObservation] = []
        for run in runs:
            t0_options.extend(
                _observations_during(
                    db,
                    candidate_id,
                    run.started_at,
                    run.completed_at or run.started_at,
                    source="tiktok",
                )
            )
        t0 = max(t0_options, key=lambda row: (row.observed_at, row.id)) if t0_options else None
        t1_options = _observations_during(
            db,
            candidate_id,
            t1_run.started_at,
            t1_run.completed_at or t1_run.started_at,
            source="tiktok_observe",
        )
        t2_options = _observations_during(
            db,
            candidate_id,
            t2_run.started_at,
            t2_run.completed_at or t2_run.started_at,
            source="tiktok_observe",
        )
        t1 = t1_options[-1] if t1_options else None
        t2 = t2_options[-1] if t2_options else None
        member.t0_observation_id = t0.id if t0 else None
        member.t1_observation_id = t1.id if t1 else None
        member.t2_observation_id = t2.id if t2 else None
        member.right_censored = bool(t2 and t2.view_count is None)
        if member.right_censored:
            member.censor_reason = (
                "Provider returned no metrics at T2; historical measurement evidence marked "
                "the post unavailable/private."
            )
            member.censored_at = t2.observed_at
        else:
            member.censor_reason = None
            member.censored_at = None
        preserved = (
            _latest_valid_observation(db, candidate_id, before=t2.observed_at)
            if t2 is not None and t2.view_count is None
            else t2
        )
        member.outcome_label = classify_trajectory(
            t0.view_count if t0 else None,
            preserved.view_count if preserved else None,
        )
        member.outcome_rule_version = TRAJECTORY_RULE_VERSION

    _attach_historical_run(t1_run, cohort_id=cohort.id, milestone="T1")
    _attach_historical_run(t2_run, cohort_id=cohort.id, milestone="T2")
    db.flush()
    return cohort


def backfill_cohort_24(db: Session) -> ResearchCohort:
    acquisition = db.get(AcquisitionRun, 24)
    t1_run = db.get(DiscoveryRun, 16)
    if acquisition is None or t1_run is None:
        raise ValueError("Cohort #24 backfill requires acquisition run #24 and discovery run #16")
    existing = (
        db.query(ResearchCohort)
        .filter(ResearchCohort.cohort_key == COHORT_24_KEY)
        .one_or_none()
    )
    if existing is not None:
        member_ids = [member.candidate_id for member in existing.members]
    else:
        accepted_ids = _raw_candidate_ids_for_run(db, acquisition)
        # acquisition_run_id is mutable on later duplicate acquisition. The
        # immutable evidence that #24 persisted a member is its T0 observation
        # inside run #24's exact execution window.
        member_ids = [
            candidate_id
            for candidate_id in accepted_ids
            if _observations_during(
                db,
                candidate_id,
                acquisition.started_at,
                acquisition.completed_at or acquisition.started_at,
                source="tiktok",
            )
        ]
    if len(member_ids) != 7:
        raise ValueError(f"Expected 7 cohort #24 members, found {len(member_ids)}")

    snapshot = _profile_run_snapshot(acquisition)
    t0_at = ensure_utc(acquisition.completed_at or acquisition.started_at)
    targets = cohort_targets(t0_at, "tiktok_fresh_search")
    cohort, created = _get_or_create_cohort(
        db,
        COHORT_24_KEY,
        name="TikTok Fresh Search Cohort #24",
        source="tiktok",
        provider=acquisition.provider,
        profile="tiktok_fresh_search",
        acquisition_run_id=24,
        source_run_ids_json=[24],
        created_at=utcnow(),
        t0_at=t0_at,
        target_t1_window_start=targets["target_t1_window_start"],
        target_t1_at=targets["target_t1_at"],
        target_t1_window_end=targets["target_t1_window_end"],
        target_t2_at=targets["target_t2_at"],
        status="ACTIVE",
        config_fingerprint=config_fingerprint(snapshot),
        config_snapshot_json=snapshot,
        notes="Backfilled from acquisition run #24 and generic observation run #16.",
        outcome_rule_version=TRAJECTORY_RULE_VERSION,
    )
    if not created:
        cohort.status = "ACTIVE"

    for candidate_id in member_ids:
        member = _get_or_create_member(db, cohort, candidate_id)
        t0_options = _observations_during(
            db,
            candidate_id,
            acquisition.started_at,
            acquisition.completed_at or acquisition.started_at,
            source="tiktok",
        )
        t1_options = _observations_during(
            db,
            candidate_id,
            t1_run.started_at,
            t1_run.completed_at or t1_run.started_at,
            source="tiktok_observe",
        )
        member.t0_observation_id = t0_options[-1].id if t0_options else None
        member.t1_observation_id = t1_options[-1].id if t1_options else None
        member.t2_observation_id = None
        member.outcome_label = None
        member.right_censored = False
        member.censor_reason = None
        member.censored_at = None

    _attach_historical_run(t1_run, cohort_id=cohort.id, milestone="T1")
    db.flush()
    return cohort


def backfill_known_cohorts(db: Session) -> list[ResearchCohort]:
    """Idempotently link the two known experiments without adding observations."""
    cohorts = [backfill_mature_reference(db), backfill_cohort_24(db)]
    db.commit()
    return cohorts


def create_cohort_from_acquisition(
    db: Session,
    acquisition: AcquisitionRun,
    *,
    label: str | None = None,
) -> ResearchCohort | None:
    """Register only newly persisted candidates from one completed acquisition."""
    meta = dict(acquisition.run_metadata_json or {})
    member_ids = [int(value) for value in (meta.get("new_candidate_ids") or [])]
    member_ids = list(dict.fromkeys(member_ids))
    if acquisition.status != "succeeded" or not member_ids:
        return None
    key = f"acquisition-run-{acquisition.id}"
    existing = db.query(ResearchCohort).filter(ResearchCohort.cohort_key == key).one_or_none()
    if existing is not None:
        return existing
    snapshot = _profile_run_snapshot(acquisition)
    t0_at = ensure_utc(acquisition.completed_at or acquisition.started_at)
    targets = cohort_targets(t0_at, acquisition.profile or "tiktok_fresh_search")
    cohort = ResearchCohort(
        cohort_key=key,
        name=label or f"TikTok Fresh Search Cohort #{acquisition.id}",
        source=acquisition.source,
        provider=acquisition.provider,
        profile=acquisition.profile,
        acquisition_run_id=acquisition.id,
        source_run_ids_json=[acquisition.id],
        created_at=utcnow(),
        t0_at=t0_at,
        target_t1_window_start=targets["target_t1_window_start"],
        target_t1_at=targets["target_t1_at"],
        target_t1_window_end=targets["target_t1_window_end"],
        target_t2_at=targets["target_t2_at"],
        status="ACTIVE",
        config_snapshot_json=snapshot,
        config_fingerprint=config_fingerprint(snapshot),
        notes="Created atomically from the new candidates persisted by this acquisition run.",
        outcome_rule_version=TRAJECTORY_RULE_VERSION,
    )
    db.add(cohort)
    db.flush()
    for candidate_id in member_ids:
        candidate = db.get(ContentCandidate, candidate_id)
        if candidate is None:
            raise ValueError(f"Acquisition metadata references missing candidate {candidate_id}")
        observations = _observations_during(
            db,
            candidate_id,
            acquisition.started_at,
            acquisition.completed_at or acquisition.started_at,
            source=acquisition.source,
        )
        if not observations:
            raise ValueError(f"Candidate {candidate_id} has no T0 observation in acquisition run")
        db.add(
            ResearchCohortMember(
                cohort_id=cohort.id,
                candidate_id=candidate_id,
                t0_observation_id=observations[-1].id,
                included_at=utcnow(),
                outcome_rule_version=TRAJECTORY_RULE_VERSION,
                right_censored=False,
            )
        )
    db.commit()
    return cohort


def start_cohort(
    db: Session,
    *,
    source: str,
    profile: str,
    limit: int,
    client: ApifyClient | None = None,
    cfg: dict[str, Any] | None = None,
) -> tuple[AcquisitionRun, ResearchCohort | None]:
    acquisition = run_acquisition(
        db,
        source,
        profile=profile,
        limit=limit,
        client=client,
        cfg=cfg,
    )
    return acquisition, create_cohort_from_acquisition(db, acquisition)


def _milestone_attr(milestone: str) -> str:
    return {"T1": "t1_observation_id", "T2": "t2_observation_id"}[milestone]


def _prior_censored_ids(db: Session, cohort_id: int, milestone: str) -> set[int]:
    runs = (
        db.query(DiscoveryRun)
        .filter(DiscoveryRun.cohort_id == cohort_id, DiscoveryRun.milestone == milestone)
        .all()
    )
    ids: set[int] = set()
    for run in runs:
        metadata = dict(run.run_metadata_json or {})
        ids.update(int(value) for value in (metadata.get("censored_candidate_ids") or []))
    return ids


def _measurement_due(cohort: ResearchCohort, milestone: str, now: datetime) -> bool:
    now = ensure_utc(now)
    if milestone == "T1":
        return now >= ensure_utc(cohort.target_t1_window_start)
    return now >= ensure_utc(cohort.target_t2_at)


def _raw_item_keys(item: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for name in ("url", "webVideoUrl", "input", "postUrl", "originalUrl"):
        value = item.get(name)
        if value:
            keys.add(str(value))
            video_id = tiktok_video_id(str(value), item)
            if video_id:
                keys.add(video_id)
    video_id = tiktok_video_id(None, item)
    if video_id:
        keys.add(video_id)
    return keys


def observe_cohort(
    db: Session,
    cohort_id: int,
    milestone: str,
    *,
    client: ApifyClient | None = None,
    cfg: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> CohortObservationResult:
    """Observe exactly unresolved cohort members through the existing TikTok pipeline."""
    milestone = milestone.strip().upper()
    if milestone not in {"T1", "T2"}:
        raise ValueError("milestone must be T1 or T2")
    cohort = db.get(ResearchCohort, cohort_id)
    if cohort is None:
        raise ValueError(f"Cohort {cohort_id} not found")
    if cohort.source != "tiktok":
        raise ValueError("Exact cohort observation v1 currently supports TikTok cohorts")
    members = (
        db.query(ResearchCohortMember)
        .filter(ResearchCohortMember.cohort_id == cohort.id)
        .order_by(ResearchCohortMember.id.asc())
        .all()
    )
    resolved_censored = _prior_censored_ids(db, cohort.id, milestone)
    attr = _milestone_attr(milestone)
    unresolved = [
        member
        for member in members
        if getattr(member, attr) is None and member.candidate_id not in resolved_censored
    ]
    if not unresolved:
        return CohortObservationResult(
            cohort=cohort,
            milestone=milestone,
            run=None,
            requested_candidate_ids=[],
            observed_candidate_ids=[],
            censored_candidate_ids=sorted(resolved_censored),
            unavailable_candidate_ids=[],
            no_op=True,
        )
    now = ensure_utc(now or utcnow())
    if not _measurement_due(cohort, milestone, now):
        target = (
            cohort.target_t1_window_start if milestone == "T1" else cohort.target_t2_at
        )
        raise ValueError(
            f"{milestone} is not due until {ensure_utc(target).isoformat()} "
            f"({format_toronto(target)})"
        )

    candidates = [db.get(ContentCandidate, member.candidate_id) for member in unresolved]
    if any(candidate is None or not candidate.url for candidate in candidates):
        raise ValueError("Every unresolved cohort member must reference a stored candidate URL")
    candidate_rows = [candidate for candidate in candidates if candidate is not None]
    urls = [str(candidate.url) for candidate in candidate_rows]
    discovery_cfg = cfg or load_discovery_config()
    tt = _apify_tiktok_cfg(discovery_cfg)
    frozen = dict(cohort.config_snapshot_json or {})
    actor_id = str(frozen.get("actor_id") or tt["actor_id"])
    client = client or ApifyClient()
    run = DiscoveryRun(
        kind="observe_tiktok_cohort",
        started_at=now,
        queries_used=[],
        candidates_found=len(candidate_rows),
        observations_written=0,
        api_errors=[],
        cohort_id=cohort.id,
        milestone=milestone,
        run_metadata_json={
            "requested_candidate_ids": [candidate.id for candidate in candidate_rows],
            "requested_urls": urls,
            "selection": "exact_cohort_membership",
            "acquires_candidates": False,
            "runs_llm": False,
        },
    )
    db.add(run)
    db.flush()
    try:
        result = client.run_actor_result(
            actor_id,
            {**_base_actor_input(), "postURLs": urls, "resultsPerPage": 1},
            timeout_secs=int(tt["timeout_secs"]),
            max_total_charge_usd=float(tt["max_charge_usd"]),
            max_items=len(urls),
        )
    except Exception as exc:
        run.api_errors = [{"type": "apify", "message": str(exc)}]
        run.completed_at = utcnow()
        db.commit()
        raise
    run.apify_run_id = result.run_id
    run.apify_dataset_id = result.dataset_id
    run.actual_cost = result.actual_cost
    run.currency = result.currency
    if result.status != "SUCCEEDED":
        run.api_errors = [
            {"type": "actor", "message": f"Apify actor ended with status {result.status}"}
        ]
        run.completed_at = utcnow()
        db.commit()
        raise AcquisitionError(f"Apify actor {actor_id} ended with status {result.status}")

    videos: dict[str, Any] = {}
    explicit_errors: dict[str, dict[str, Any]] = {}
    for item in result.items:
        if not isinstance(item, dict):
            continue
        keys = _raw_item_keys(item)
        if item.get("errorCode") or item.get("_warning"):
            for key in keys:
                explicit_errors[key] = item
            continue
        video = item_to_video(item, max_short_seconds=float(tt["max_short_seconds"]))
        if video is None:
            continue
        for key in keys | {video.external_id, video.url}:
            if key:
                videos[str(key)] = video

    observed_ids: list[int] = []
    censored_ids: list[int] = []
    unavailable_ids: list[int] = []
    members_by_candidate = {member.candidate_id: member for member in unresolved}
    for candidate in candidate_rows:
        member = members_by_candidate[candidate.id]
        keys = {candidate.url or "", candidate.external_id or ""}
        video = next((videos[key] for key in keys if key and key in videos), None)
        if video is not None and any(
            value is not None
            for value in (
                video.views,
                video.likes,
                video.comments,
                video.shares,
                video.favorite_count,
                video.channel_subscriber_count,
            )
        ):
            refresh_candidate_metrics(candidate, video)
            observation = record_observation(
                db,
                candidate,
                video,
                source=f"tiktok_cohort_{milestone.lower()}",
            )
            apply_signals(db, candidate, discovery_cfg)
            setattr(member, attr, observation.id)
            member.right_censored = False
            member.censor_reason = None
            member.censored_at = None
            observed_ids.append(candidate.id)
            continue
        error = next((explicit_errors[key] for key in keys if key and key in explicit_errors), None)
        if error is not None:
            code = str(error.get("errorCode") or "PROVIDER_WARNING")
            message = str(error.get("error") or error.get("_warning") or "unavailable")
            member.right_censored = True
            member.censor_reason = f"{code}: {message}"[:1000]
            member.censored_at = utcnow()
            censored_ids.append(candidate.id)
        else:
            unavailable_ids.append(candidate.id)

    run.observations_written = len(observed_ids)
    run.completed_at = utcnow()
    metadata = dict(run.run_metadata_json or {})
    metadata.update(
        {
            "observed_candidate_ids": observed_ids,
            "censored_candidate_ids": censored_ids,
            "unavailable_candidate_ids": unavailable_ids,
        }
    )
    run.run_metadata_json = metadata
    if milestone == "T2" and len(observed_ids) + len(censored_ids) == len(unresolved):
        cohort.status = "COMPLETED"
        for member in unresolved:
            t0 = db.get(CandidateObservation, member.t0_observation_id)
            t2 = db.get(CandidateObservation, member.t2_observation_id)
            if t2 is not None and t2.view_count is not None:
                member.outcome_label = classify_trajectory(
                    t0.view_count if t0 else None, t2.view_count
                )
                member.outcome_rule_version = TRAJECTORY_RULE_VERSION
    db.commit()
    return CohortObservationResult(
        cohort=cohort,
        milestone=milestone,
        run=run,
        requested_candidate_ids=[candidate.id for candidate in candidate_rows],
        observed_candidate_ids=observed_ids,
        censored_candidate_ids=censored_ids,
        unavailable_candidate_ids=unavailable_ids,
    )


def milestone_state(
    db: Session,
    cohort: ResearchCohort,
    milestone: str,
    *,
    now: datetime | None = None,
) -> str:
    milestone = milestone.upper()
    members = db.query(ResearchCohortMember).filter_by(cohort_id=cohort.id).all()
    if milestone == "T0":
        links = [member.t0_observation_id for member in members]
    else:
        attr = _milestone_attr(milestone)
        links = [getattr(member, attr) for member in members]
    prior_censored = _prior_censored_ids(db, cohort.id, milestone) if milestone != "T0" else set()
    complete = sum(value is not None for value in links)
    resolved = complete + len(prior_censored)
    if members and resolved >= len(members):
        return "RIGHT_CENSORED" if prior_censored or (
            milestone == "T2" and any(member.right_censored for member in members)
        ) else "COMPLETE"
    if resolved:
        return "PARTIAL"
    now = ensure_utc(now or utcnow())
    if milestone == "T0":
        return "COMPLETE" if complete else "PARTIAL"
    if milestone == "T1":
        start = ensure_utc(cohort.target_t1_window_start)
        end = ensure_utc(cohort.target_t1_window_end)
        if now < start:
            return "UPCOMING"
        return "DUE" if now <= end else "OVERDUE"
    target = ensure_utc(cohort.target_t2_at)
    if now < target:
        return "UPCOMING"
    return "DUE" if now <= target + timedelta(hours=24) else "OVERDUE"


def _actual_at(db: Session, cohort_id: int, milestone: str) -> datetime | None:
    attr = {"T0": "t0_observation_id", "T1": "t1_observation_id", "T2": "t2_observation_id"}[milestone]
    members = db.query(ResearchCohortMember).filter_by(cohort_id=cohort_id).all()
    observations = [
        db.get(CandidateObservation, getattr(member, attr))
        for member in members
        if getattr(member, attr) is not None
    ]
    times = [observation.observed_at for observation in observations if observation is not None]
    return max(times) if times else None


def cohort_costs(db: Session, cohort: ResearchCohort) -> dict[str, Any]:
    source_ids = list(cohort.source_run_ids_json or [])
    acquisition_rows = [db.get(AcquisitionRun, int(run_id)) for run_id in source_ids]
    acquisition_values = [
        row.actual_cost for row in acquisition_rows if row is not None and row.actual_cost is not None
    ]
    acquisition = round(sum(acquisition_values), 6) if acquisition_values else None
    observation_runs = (
        db.query(DiscoveryRun)
        .filter(DiscoveryRun.cohort_id == cohort.id)
        .order_by(DiscoveryRun.id.asc())
        .all()
    )
    by_milestone: dict[str, list[float]] = {"T1": [], "T2": []}
    currencies = [row.currency for row in acquisition_rows if row is not None and row.currency]
    for run in observation_runs:
        if run.milestone in by_milestone and run.actual_cost is not None:
            by_milestone[run.milestone].append(run.actual_cost)
        if run.currency:
            currencies.append(run.currency)
    t1 = round(sum(by_milestone["T1"]), 6) if by_milestone["T1"] else None
    t2 = round(sum(by_milestone["T2"]), 6) if by_milestone["T2"] else None
    known_values = [value for value in (acquisition, t1, t2) if value is not None]
    all_known = acquisition is not None and all(
        value is not None
        for value, expected in ((t1, milestone_state(db, cohort, "T1")), (t2, milestone_state(db, cohort, "T2")))
        if expected in {"COMPLETE", "RIGHT_CENSORED"}
    )
    return {
        "acquisition": acquisition,
        "t1": t1,
        "t2": t2,
        "total": round(sum(known_values), 6) if known_values and all_known else None,
        "known_spend": round(sum(known_values), 6) if known_values else None,
        "currency": currencies[0] if currencies else "USD",
    }


def _safe_drift(cohort: ResearchCohort) -> bool | None:
    snapshot = dict(cohort.config_snapshot_json or {})
    if snapshot.get("composite") or snapshot.get("snapshot_type") == "composite":
        return None
    try:
        return config_drift(
            snapshot,
            cohort.config_fingerprint,
        )
    except Exception:
        return None


def cohort_member_views(db: Session, cohort: ResearchCohort) -> list[dict[str, Any]]:
    members = (
        db.query(ResearchCohortMember)
        .filter_by(cohort_id=cohort.id)
        .order_by(ResearchCohortMember.id.asc())
        .all()
    )
    rows: list[dict[str, Any]] = []
    for member in members:
        candidate = db.get(ContentCandidate, member.candidate_id)
        t0 = db.get(CandidateObservation, member.t0_observation_id) if member.t0_observation_id else None
        t1 = db.get(CandidateObservation, member.t1_observation_id) if member.t1_observation_id else None
        t2 = db.get(CandidateObservation, member.t2_observation_id) if member.t2_observation_id else None
        latest = _latest_valid_observation(db, member.candidate_id)

        def velocity(a: CandidateObservation | None, b: CandidateObservation | None) -> float | None:
            if a is None or b is None or a.view_count is None or b.view_count is None:
                return None
            elapsed = (ensure_utc(b.observed_at) - ensure_utc(a.observed_at)).total_seconds() / 3600
            return round((b.view_count - a.view_count) / elapsed, 2) if elapsed > 0 else None

        rows.append(
            {
                "candidate_id": member.candidate_id,
                "creator": (candidate.creator or candidate.channel_name) if candidate else None,
                "query": candidate.source_query if candidate else None,
                "url": candidate.url if candidate else None,
                "t0_views": t0.view_count if t0 else None,
                "t1_views": t1.view_count if t1 else None,
                "t2_views": t2.view_count if t2 else None,
                "latest_views": latest.view_count if latest else None,
                "early_vph": velocity(t0, t1),
                "later_vph": velocity(t1, t2),
                "trajectory": member.outcome_label,
                "outcome": member.outcome_label,
                "current_labels": list(candidate.discovery_labels or []) if candidate else [],
                "right_censored": bool(member.right_censored),
                "censor_reason": member.censor_reason,
            }
        )
    return rows


def cohort_view(db: Session, cohort: ResearchCohort, *, now: datetime | None = None) -> dict[str, Any]:
    members = db.query(ResearchCohortMember).filter_by(cohort_id=cohort.id).all()
    outcome_counts = {name: 0 for name in ("BREAKOUT", "STRONG_RISER", "MODERATE", "STALLED")}
    for member in members:
        if member.outcome_label in outcome_counts:
            outcome_counts[member.outcome_label] += 1
    outcome_counts["RIGHT_CENSORED"] = sum(bool(member.right_censored) for member in members)
    t1_state = milestone_state(db, cohort, "T1", now=now)
    t2_state = milestone_state(db, cohort, "T2", now=now)
    next_target = None
    if t1_state not in {"COMPLETE", "RIGHT_CENSORED"}:
        next_target = cohort.target_t1_at
    elif t2_state not in {"COMPLETE", "RIGHT_CENSORED"}:
        next_target = cohort.target_t2_at
    return {
        "id": cohort.id,
        "name": cohort.name,
        "source": cohort.source,
        "provider": cohort.provider,
        "profile": cohort.profile,
        "status": cohort.status,
        "acquisition_run_id": cohort.acquisition_run_id,
        "source_run_ids": list(cohort.source_run_ids_json or []),
        "member_count": len(members),
        "t0_display": format_toronto(cohort.t0_at),
        "t1_status": t1_state,
        "t2_status": t2_state,
        "t2_target_display": format_toronto(cohort.target_t2_at),
        "next_target_display": format_toronto(next_target) if next_target else None,
        "config_fingerprint": cohort.config_fingerprint,
        "config_drift": _safe_drift(cohort),
        "outcome_counts": outcome_counts,
        "notes": cohort.notes,
    }


def list_cohort_views(db: Session, *, now: datetime | None = None) -> dict[str, list[dict[str, Any]]]:
    cohorts = db.query(ResearchCohort).order_by(ResearchCohort.t0_at.desc(), ResearchCohort.id.desc()).all()
    views = [cohort_view(db, cohort, now=now) for cohort in cohorts]
    active = [view for view in views if view["status"] != "COMPLETED"]
    completed = [view for view in views if view["status"] == "COMPLETED"]
    due = [
        view
        for view in active
        if view["t1_status"] in {"DUE", "OVERDUE", "PARTIAL"}
        or view["t2_status"] in {"DUE", "OVERDUE", "PARTIAL"}
    ]
    return {"due_measurements": due, "active_cohorts": active, "completed_cohorts": completed}


def cohort_detail_view(db: Session, cohort_id: int, *, now: datetime | None = None) -> dict[str, Any] | None:
    cohort = db.get(ResearchCohort, cohort_id)
    if cohort is None:
        return None
    base = cohort_view(db, cohort, now=now)
    t0_actual = _actual_at(db, cohort.id, "T0")
    t1_actual = _actual_at(db, cohort.id, "T1")
    t2_actual = _actual_at(db, cohort.id, "T2")
    timing = {
        "t0_display": format_toronto(cohort.t0_at),
        "t0_actual_display": format_toronto(t0_actual),
        "t0_status": milestone_state(db, cohort, "T0", now=now),
        "t1_target_display": format_toronto(cohort.target_t1_at),
        "t1_window_start_display": format_toronto(cohort.target_t1_window_start),
        "t1_window_end_display": format_toronto(cohort.target_t1_window_end),
        "t1_actual_display": format_toronto(t1_actual),
        "t1_status": milestone_state(db, cohort, "T1", now=now),
        "t2_target_display": format_toronto(cohort.target_t2_at),
        "t2_actual_display": format_toronto(t2_actual),
        "t2_status": milestone_state(db, cohort, "T2", now=now),
    }
    analyses = (
        db.query(CohortDifferentialAnalysis)
        .filter_by(cohort_id=cohort.id)
        .order_by(CohortDifferentialAnalysis.created_at.desc(), CohortDifferentialAnalysis.id.desc())
        .all()
    )
    history = [
        {
            "id": row.id,
            "created_at": row.created_at,
            "created_at_display": format_toronto(row.created_at),
            "prompt_version": row.prompt_version,
            "provider": row.provider,
            "model": row.model,
            "evidence_mode": row.evidence_mode,
            "evidence_available": list(row.evidence_available or []),
            "evidence_missing": list(row.evidence_missing or []),
            "result_json": dict(row.result_json or {}),
        }
        for row in analyses
    ]
    return {
        "cohort": base,
        "timing": timing,
        "costs": cohort_costs(db, cohort),
        "members": cohort_member_views(db, cohort),
        "config_snapshot_json": json.dumps(
            cohort.config_snapshot_json or {}, indent=2, sort_keys=True, ensure_ascii=False
        ),
        "latest_analysis": history[0] if history else None,
        "analysis_history": history,
    }
